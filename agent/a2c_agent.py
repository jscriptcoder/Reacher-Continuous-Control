import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .actor import Actor
from .critic import Critic
#from .policy import Policy
from .device import device
from .data import Data

class A2CAgent:
    def __init__(self, config):
        
        np.random.seed(config.seed)
        torch.manual_seed(config.seed)
        
        self.config = config
        self.data = Data()
        
#        self.policy = Policy(config.state_size, config.action_size)

        self.policy = Actor(config.state_size, 
                            config.action_size, 
                            config.hidden_actor,
                            config.activ_actor)
        
        self.value = Critic(config.state_size, 
                            config.hidden_critic,
                            config.activ_critic)
        
#        self.optim = config.optim(self.policy.parameters(), lr=config.lr)
        
        self.optim_policy = config.optim_actor(self.policy.parameters(), 
                                               lr=config.lr_actor)
        self.optim_value = config.optim_critic(self.value.parameters(), 
                                               lr=config.lr_critic)
        
        self.reset()
    
    def reset(self):
        self.state = self.config.envs.reset()
        self.steps_done = 0
        self.done = False
        
    def act(self, state):
        self.policy.eval()
        with torch.no_grad():
#            action, _, _, _, _ = self.policy(state)
            action, _, _, _ = self.policy(state)
        self.policy.train()
        
        return action
    
    def collect_data(self):
        steps = self.config.steps
        envs = self.config.envs
        state = self.state
        
        self.data.clear()
                
        for _ in range(steps):
            state = torch.FloatTensor(state).to(device)
            
#            _, action, log_prob, entropy, value = self.policy(state)
            _, action, log_prob, entropy = self.policy(state)
            value = self.value(state)
            
            next_state, reward, done, _ = envs.step(action.cpu().numpy())
            
            done = np.array(done)
            reward = torch.FloatTensor(reward).unsqueeze(-1).to(device)
            mask = torch.FloatTensor(1 - done).unsqueeze(-1).to(device)
            
            self.data.add(states=state, 
                          actions=action, 
                          log_probs=log_prob, 
                          entropies=entropy, 
                          values=value, 
                          rewards=reward, 
                          masks=mask)
            
            state = next_state
            
            self.steps_done += 1
            
            if done.any():
                self.done = True
                break
        
        self.state = state
        
        # Let's estimate the next value
#        _, _, _, _, next_value = self.policy(state)
        next_value = self.value(state)
        
        return next_value
    
    def compute_return(self, next_value):
        num_agents = self.config.num_agents
        use_gae = self.config.use_gae
        gamma = self.config.gamma
        lamda = self.config.lamda
        
        values = self.data.get('values') + [next_value]
        rewards = self.data.get('rewards')
        masks = self.data.get('masks')
        
        returns = []
        R = next_value.detach()
        GAE = torch.zeros((num_agents, 1))
        
        for i in reversed(range(len(rewards))):
            reward = rewards[i]
            
            if use_gae:
                value = values[i].detach()
                value_next = values[i+1].detach()
                
                # δ = r + γV(s') - V(s)
                delta = reward + gamma * value_next * masks[i] - value
                
                # GAE = δ' + λγδ
                GAE = delta + lamda * gamma * GAE * masks[i]
                
                returns.insert(0, GAE + value)
            else:
                # R = r + γV(s')
                R = reward + gamma * R * masks[i]
                
                returns.insert(0, R)
        
        return returns
    
    def update(self, policy_loss, value_loss):
#        grad_clip = self.config.grad_clip
        grad_clip_actor = self.config.grad_clip_actor
        grad_clip_critic = self.config.grad_clip_critic
        
#        loss = policy_loss + value_loss
#        
#        self.optim.zero_grad()
#        
#        los.backward()
#        
#        if grad_clip is not None:
#            nn.utils.clip_grad_norm_(self.policy.parameters(), grad_clip)
#        
#        self.optim.step()
        
        self.optim_policy.zero_grad()
        policy_loss.backward()
        
        if grad_clip_actor is not None:
            nn.utils.clip_grad_norm_(self.policy.parameters(), grad_clip_actor)
        
        self.optim_policy.step()
        
        self.optim_value.zero_grad()
        value_loss.backward()
        
        if grad_clip_critic is not None:
            nn.utils.clip_grad_norm_(self.value.parameters(), grad_clip_critic)
        
        self.optim_value.step()
    
    def learn(self, returns):
        ent_weight = self.config.ent_weight
        val_loss_weight = self.config.val_loss_weight
        
        log_probs = self.data.get('log_probs')
        entropies = self.data.get('entropies')
        values = self.data.get('values')
        
        # List to torch.Tensor
        log_probs = torch.cat(log_probs)
        entropies = torch.cat(entropies)
        values = torch.cat(values)
        returns = torch.cat(returns)
        
        # A(s, a) = r + γV(s') - V(s)
        advantages = returns - values.detach()
        
        policy_loss = (-log_probs * advantages - ent_weight * entropies).mean()
        value_loss = val_loss_weight * (returns - values).pow(2).mean()
        
        self.update(policy_loss, value_loss)
        
        return policy_loss, value_loss
    
    def step(self):
        
        next_value = self.collect_data()
        returns = self.compute_return(next_value)
        policy_loss, value_loss = self.learn(returns)
        
        return policy_loss, value_loss
    
    def train(self):
        num_episodes = self.config.num_episodes
        env_solved = self.config.env_solved
        envs = self.config.envs
        
        for i_episode in range(1, num_episodes+1):
            self.reset()
            while True:
                policy_loss, value_loss = self.step()
                if self.done:
                    break
            
            score = self.eval_episode()
            
            print('\rEpisode {}\tPolicy loss: {:.3f}\tValue loss: {:.3f}\tAvg Score: {:.3f}'\
                  .format(i_episode, 
                          policy_loss, 
                          value_loss, 
                          score), end='')
            
            if score >= env_solved:
                print('Environment solved!')
                break;
        
        envs.close()
    
    def eval_episode(self):
        envs = self.config.envs
        times_solved = self.config.times_solved
        
        total_score = 0
        
        for _ in range(times_solved):
            state = envs.reset()
            while True:
                action = self.act(state)
                state, reward, done, _ = envs.step(action.cpu().numpy())
                
                avg_reward = np.mean(reward)
                total_score += avg_reward
    
                if np.array(done).all():
                    break
                
        return total_score / times_solved
    
    def summary(self, agent_name='A2C Agent'):
        print('{}:'.format(agent_name))
        print('==========')
        print('')
        print('Policy Network:')
        print('---------------')
        print(self.policy)
        print('')
        print('Value Network:')
        print('--------------')
        print(self.value)