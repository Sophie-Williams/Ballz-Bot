from __future__ import print_function

# Main imports
from PIL import Image
from pytesser import *
import subprocess
import math

# Used for debugging
import time
import pprint
import pygame
import sys

pygame.init() # Used for debugging physics

class Android(object): # Interface for using adb

    def __init__(self):
        pass

    def _call(self, cmd):
        subprocess.call("adb " + cmd, shell=True)

    def screenshot(self, fn="/sdcard/screen.png", sn="screen.png"):
        self._call("shell screencap " + fn)
        self._call("pull " + fn)
        return Image.open(sn)

    def tap(self, x, y):
        self._call("shell input tap {} {}".format(x, y))

    def swipe(self, x1, y1, x2, y2, ms=500):
        self._call("shell input swipe {} {} {} {} {}".format(int(x1), int(y1), int(x2), int(y2), ms))

    def swipe_angle(self, x, y, angle, dist=80): # Swipe to shot ball at angle from x,y coord
        angle = angle - 90
        angle *= .993
        angle += 90
        rad = math.radians(180+angle)
        dx = math.cos(rad) * dist
        dy = math.sin(rad) * dist
        self.swipe(x, y, x + dx, y - dy, 600)


def get_int(image): # int(Image)
    return int(image_to_string(image)
                   .replace('O','0')
                   .replace('l','1')
                   .replace('.','')
                   .replace('A', '4')
                   .replace('S', '5')
                   .replace('x', '')
                   .replace('X', '')
                   .replace('<', '')
                   .replace('>', '').strip())

class Analyzer(object): # Screenshot -> Current Game State

    # Y: y coord, X: x coord
    # W: width, H: height
    # C: color

    BG_C = (32,32,32)
    TOP_Y = 160
    BOT_Y = 1585
    BAR_H = 48
    BALL_C = (255,255,255)
    BALL_W = 42
    BLOCKS_X = 23
    BLOCKS_Y = 160
    BLOCKS_W = 1034
    BLOCKS_H = 1063
    BLOCKS_SPACE_X = 151
    BLOCKS_SPACE_Y = 155
    BLOCK_W = 120
    RING_C = (231,236,68)
    NOTBLOCK_C = [BALL_C, RING_C, BG_C, (86,86,86), (84,84,84), (33,33,33), (66,66,66)]

    def __init__(self, image):
        self.image = image.convert('RGB')

    def _get_ball_pos(self, screen): # x,y pos of ball
        x = 0
        while screen.getpixel((x, 1560)) == self.BG_C:
            x += 1
        return [x + 21, 1560]

    def _get_num_balls(self, ballpos, screen): # number of balls
        x, y = ballpos
        try:
            im = screen.crop((max(x-80, 0),y-80,min(x+90, 1080),y-22))
            return get_int(im)
        except Exception as e:
            print(e)
            return 1

    def _get_block_type(self, block): # type/value of block or ring
        r, g, b = block.getpixel((40,40))
        if r != g != b and (r,g,b) not in self.NOTBLOCK_C:
            return get_int(block)
        elif block.getpixel((60,60)) == self.BALL_C:
            return -1
        elif block.getpixel((37,46)) == self.RING_C:
            return -2
        return 0

    def _get_blocks(self, blocks): # Matrix of all blocks w/values and rings
        
        grid = [ [0 for c in xrange(7)] for r in xrange(7) ]
        
        w, h = blocks.size
        dx = self.BLOCKS_SPACE_X
        dy = self.BLOCKS_SPACE_Y

        row = 0
        col = 0
        
        for x in xrange(0, w, dx):
            for y in xrange(0, h, dy):
                block = blocks.crop((x, y, x + self.BLOCK_W, y + self.BLOCK_W))
                
                grid[row][col] = self._get_block_type(block)

                row += 1
                
            col += 1
            row = 0

        return grid

    def get_state(self): # The current game state from the screenshot
        
        board = self.image.crop((0, self.TOP_Y, self.image.size[0], self.BOT_Y))

        ball_pos = self._get_ball_pos(self.image)

        nballs = self._get_num_balls(ball_pos, self.image)
        
        blocks = board.crop((self.BLOCKS_X, self.BLOCKS_Y, self.BLOCKS_X + self.BLOCKS_W, self.BLOCKS_Y + self.BLOCKS_H))
        grid = self._get_blocks(blocks)

        blocks.save('blocks.png') # Saves for debug

        return ball_pos, grid, nballs

class Simulator(object): # Uses game state to simulate plays @ diff angles

    def __init__(self, grid, ball_pos):
        self.grid = grid
        self.ball_pos = ball_pos

    ###### Game Objects ######

    class Block(object): # Standard Block

        def __init__(self, row, col, value, w=134, h=137):
            self.r = row
            self.c = col
            self.x = 22 + col * 151
            self.y = 320 + row * 154
            self.value = value
            self.w = w
            self.h = h

        def draw(self, surface, color=None):
            if not color:
                x = self.value * 10 % 255
                color = (100, x, x)
            pygame.draw.rect(surface, color, (int(self.x/2), int(self.y/2), self.w/2, self.h/2), 0)

    class Ring(object): # Extra Ball Ring

        def __init__(self, row, col, r=28):
            self.row = row
            self.col = col
            self.x = 90 + col * 151
            self.y = 388 + row * 154
            self.r = r

        def draw(self, surface, color=(250,200,250)):
            pygame.draw.circle(surface, color, (int(self.x/2), int(self.y/2)), self.r/2, 0)

    class Ball(object): # The Ball

        def __init__(self, x, y, angle, vel=8, r=21, delay=0):
            self.x = x
            self.y = y
            self.vel = vel # Speed of the ball per sim update
            self.r = r

            self.delay = delay # How long until ball begins moving

            self.ig = False # Has the ball entered the playing space
            
            self.vx = math.cos(angle) * self.vel
            self.vy = math.sin(angle) * self.vel

        def _collide(self, rect, rng, radius, alter=False): # Customizable Circular Collison Detection

            # Converts perimeter of circle into a list of discrete points and checks if any are in rect

            for a in rng:

                rad = math.radians(a)
                dx = math.cos(rad) * radius
                dy = math.sin(rad) * radius

                if rect.collidepoint((self.x + dx, self.y - dy)):

                    if alter: # Whether results of collision should effect velocities

                        if abs(dx - dy) < 1.5: # Corner Collision
                            self.vx = self.vy
                            self.vy = -self.vx
                        elif abs(dx) > abs(dy): # Top/Bottom Collision
                            self.vx *= -1
                            self.y += dy
                        else: # Left/Right Collision
                            self.vy *= -1
                            self.x -= dx
                    
                    return True

            return False
                    

        def collides_block(self, block): # Checks if ball collides with block

            rect = pygame.Rect((block.x,block.y,block.w,block.h)) # Creates a rect object for easier checking

            if self._collide(rect, xrange(0, 360, 15), self.r + 5): # Quick test before further checking (a basic optimization)

                return self._collide(rect, xrange(0, 360, 6), self.r, alter=True) # More precise collision detection

        def collides_ring(self, ring): # Ball collides w/Ring

            dx = self.x - ring.x
            dy = self.y - ring.y

            if dx**2 + dy**2 <= (self.r + ring.r)**2:
                return True

            return False

        def dist_squared_block(self, block):
            return (self.x - (block.x + block.w/2))**2 + (self.y - (block.y + block.h/2))**2

        def update(self): # Updates the balls position
            
            if self.delay > 0:
                self.delay -= 1

            else:
                
                self.x += self.vx
                self.y -= self.vy

                if self.y < 1490: # Ball has offically left start
                    self.ig = True

                if self.x < self.r:
                    self.x = self.r
                    self.vx *= -1
                elif self.x > 1080 - self.r:
                    self.x = 1080 - self.r
                    self.vx *= -1

                if self.y < self.r + 160:
                    self.y = self.r + 160
                    self.vy *= -1
                elif self.y > 1510 and self.ig: # Ball has returned
                    self.y = 1510
                    self.vy = 0
                    self.vx = 0
                    self.vel = 0

        def draw(self, surface):
            pygame.draw.circle(surface, (255,255,255), (int(self.x/2), int(self.y/2)), self.r/2, 0)

    ##########################

    def calculate_score(self, board): # Calculate score just based on remaing blocks

        score = 0
        
        coeffs = [1,1,2,3,10,40,160] # Heuristic based on blocks remaining and how low (in height) they are
        for k in xrange(7):
            for j in xrange(7):
                if board[k][j] > 0:
                    score -= board[k][j] * coeffs[k]

        return score

    def _block_landlocked(self, block, board): # Check if there is a block on all sides
        if block.r >= 1 and block.c >= 1 and block.r < 6 and block.c < 6:
            if (board[block.r + 1][block.c] > 0 and
                board[block.r - 1][block.c] > 0 and
                board[block.r][block.c + 1] > 0 and
                board[block.r][block.c - 1] > 0):

                return True

        return False

    def simulate(self, deg, nballs=1, render=False): # Run a simulation at specific angle

        if render: # Render = Running in Debug Mode
            screen = pygame.display.set_mode((1080/2, 1920/2))

        angle = math.radians(deg)
        
        board = [list(j) for j in self.grid] # Create a copy of grid
        blocks = []
        rings = []
        balls = []

        ## Add in game objects
        for r in xrange(7):
            for c in xrange(7):
                if board[r][c] > 0:
                    blocks.append(self.Block(r, c, board[r][c]))
                elif board[r][c] == -1:
                    rings.append(self.Ring(r, c))
                    

        for i in xrange(nballs):
            balls.append(self.Ball(self.ball_pos[0], self.ball_pos[1], angle, delay=i*25))
        ##
            
        loops = 0 # The number of updates in the physics sim until round is over
        score = 0 # Based on heuristics on how well the round went

        collided = []

        while True:

            if render: # If debugging,
                
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.quit()
                        sys.exit()

                pygame.display.flip()
                pygame.display.update()

                time.sleep(0.05)

                screen.fill((64,64,64))
                pygame.draw.rect(screen, (38,38,38), (0, 0, 1080/2, 160/2), 0)
                pygame.draw.rect(screen, (38,38,38), (0, 1586/2, 1080/2, 334/2), 0)

                for block in blocks:
                    block.draw(screen)

                for ring in rings:
                    ring.draw(screen)

            done = True

            for ball in balls:

                if render:
                    ball.draw(screen)

                if ball.vel != 0: # Skip collision checks if ball isnt moving
                    done = False
                else:
                    continue
                
                for block in blocks:

                    if block in collided or self._block_landlocked(block, board) or ball.dist_squared_block(block) > 15000: # If block was already collided with then it wont happend again OR if landlocked OR too far
                        continue
                    
                    elif ball.collides_block(block):
                    
                        if render:
                            block.draw(screen, (100,100,200))

                        block.value -= 1
                        board[block.r][block.c] -= 1
                        
                        if block.value == 0: # If value falls below 0 then it was destroyed
                            
                            blocks.remove(block)
                            collided.append(block)

                            if render:
                                block.draw(screen, (250,100,100))
                                
                        break

                for ring in rings:

                    if ring in collided: # Skip collision detection if it already collided
                        continue

                    elif ball.collides_ring(ring):
                        
                        collided.append(ring)

                        rings.remove(ring)

                        board[ring.row][ring.col] = 0

                        score += 15 # Reward score for getting another ball
                    
                ball.update()

            if done and not render: # If all balls are done then sim is over
                break

            loops += 1

        score += self.calculate_score(board)

        return score, loops, board
                


def print_grid(grid):
    pprint.pprint(grid)


def main(maxballs=40, angles=None, manual=False):
    
    device = Android()

    if not angles:
        angles = range(15, 180-12, 3) # Choose all angles 3 degs apart
    
    while True:

        if manual: # Allows for manually controlling program
            maxballs = int(raw_input('Num Balls > '))

        print("\n\n\nGetting Device Screen...", end='')
        an = Analyzer(device.screenshot())
        print("Done")

        print("Retrieving Game State...", end='')
        ball, grid, nballs = an.get_state()
        print("Done")

        print_grid(grid)

        sim = Simulator(grid, ball)

        sim_nballs = min(maxballs, nballs)

        best_score = -10**9 # -> -Infinity

        for ang in angles: # Try every angle and choose one w/best score
            print("Simulating {} degs -> ".format(ang), end='')
            try:
                score, loops, _ = sim.simulate(ang, min(maxballs, sim_nballs))
                print("score = {} \tloops = {}".format(score, loops))
                if score > best_score:
                    best_score = score
                    best_angle = ang
                    best_loop = loops
            except Exception as e:
                print(e)

        print("\nBest: degrees={}, score={}, pseudo-runtime={}, balls={}\n".format(best_angle, best_score, best_loop, sim_nballs))

        device.swipe_angle(ball[0], ball[1], best_angle) # Execute best move

        if not manual: # Estimate how long the round will last and wait
            seconds = int(best_loop / 120.0 + 8)
            print("Waiting {} seconds... (Tap Ctrl-C to skip)\n".format(seconds))
            try:
                while seconds > 0:
                    time.sleep(1)
                    seconds -= 1
            except KeyboardInterrupt: # Allow users to skip in the event estimated time is too long
                time.sleep(0.7)

def show_last(angle=45): # For debugging, replays last grid layout from screen.png
    an = Analyzer(Image.open('screen.png'))
    ball, grid, nballs = an.get_state()
    s = Simulator(grid, ball)
    s.simulate(angle, nballs, render=True)
        
if __name__ == "__main__":
    main()